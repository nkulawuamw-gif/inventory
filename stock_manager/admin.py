from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Shop, Item, Sale, StockTransaction, UserProfile, Profile, UserPresence, Message, Call, Meeting, MeetingParticipant, BusinessPeriod, CompanyProfile, WhatsAppSetting, WhatsAppMessage


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


@admin.register(UserPresence)
class UserPresenceAdmin(admin.ModelAdmin):
    list_display = ['user', 'is_online', 'last_seen', 'current_shop']
    list_filter = ['is_online', 'current_shop']
    search_fields = ['user__username']


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['sender', 'receiver', 'body_short', 'is_read', 'created_at']
    list_filter = ['is_read', 'created_at']
    search_fields = ['sender__username', 'receiver__username', 'body']

    def body_short(self, obj):
        return obj.body[:50]
    body_short.short_description = 'Message'


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):
    list_display = ['caller', 'callee', 'call_type', 'status', 'started_at', 'ended_at']
    list_filter = ['call_type', 'status']
    search_fields = ['caller__username', 'callee__username']


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ['name', 'meeting_code', 'host', 'meeting_type', 'is_active', 'started_at']
    list_filter = ['meeting_type', 'is_active']
    search_fields = ['name', 'meeting_code', 'host__username']


@admin.register(MeetingParticipant)
class MeetingParticipantAdmin(admin.ModelAdmin):
    list_display = ['meeting', 'user', 'joined_at', 'left_at']
    list_filter = ['meeting']
    search_fields = ['meeting__name', 'user__username']


@admin.register(BusinessPeriod)
class BusinessPeriodAdmin(admin.ModelAdmin):
    list_display = ['name', 'period_type', 'start_date', 'end_date', 'is_closed', 'closed_at', 'closed_by']
    list_filter = ['period_type', 'is_closed']
    search_fields = ['name']
    date_hierarchy = 'start_date'


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

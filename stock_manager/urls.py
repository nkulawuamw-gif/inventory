from django.urls import path
from . import views
from . import communication_views as comm
from . import whatsapp_views as wa

urlpatterns = [
    path('', views.landing_view, name='landing'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('pos/<slug:shop_slug>/', views.point_of_sale, name='point_of_sale'),
    path('inventory/<slug:shop_slug>/', views.shop_inventory, name='shop_inventory'),
    path('sales-history/', views.sales_history, name='sales_history'),
    path('receipt/<int:receipt_id>/print/', views.print_receipt, name='print_receipt'),
    path('receipt/<int:receipt_id>/', views.receipt_detail, name='receipt_detail'),
    path('receipt/<int:receipt_id>/pdf/', views.download_receipt_pdf, name='receipt_pdf'),
    path('receipt/<int:receipt_id>/email/', views.email_receipt, name='email_receipt'),
    path('receipt/<int:receipt_id>/delete/', views.delete_receipt, name='delete_receipt'),
    path('sales-export-csv/', views.export_sales_csv, name='export_sales_csv'),
    path('manage/', views.admin_manage, name='admin_manage'),
    path('financial-report/', views.financial_report, name='financial_report'),
    path('settings/', views.settings_view, name='settings'),
    path('download-template/', views.download_template, name='download_template'),
    path('download-bulk-transfer-template/', views.download_bulk_transfer_template, name='download_bulk_transfer_template'),
    path('search/', views.search_items, name='search'),
    path('export-csv/', views.export_csv, name='export_csv'),
    path('dashboard-bulk-import/', views.dashboard_bulk_import, name='dashboard_bulk_import'),

    path('heartbeat/', comm.heartbeat, name='heartbeat'),
    path('mark-offline/', comm.mark_offline, name='mark_offline'),
    path('auto-logout/', comm.auto_logout, name='auto_logout'),
    path('api/all-users/', comm.get_all_users, name='all_users'),
    path('api/online-users/', comm.get_online_users, name='online_users'),

    path('chat/', comm.chat_view, name='chat'),
    path('chat/unread/', comm.unread_count, name='unread_count'),
    path('chat/conversation/<int:user_id>/', comm.get_conversation, name='get_conversation'),
    path('chat/mark-read/', comm.mark_read, name='mark_read'),

    path('whatsapp/', wa.whatsapp_inbox, name='whatsapp_inbox'),
    path('whatsapp/webhook/', wa.whatsapp_webhook, name='whatsapp_webhook'),

    path('logout/', views.logout_view, name='logout'),
    path('change-password/', views.change_password, name='change_password'),
    path('shop/<slug:shop_slug>/', views.shop_dashboard, name='shop_dashboard'),
]

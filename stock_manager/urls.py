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
    path('sale/<int:sale_id>/', views.sale_detail, name='sale_detail'),
    path('financial-report/', views.financial_report, name='financial_report'),
    path('settings/', views.settings_view, name='settings'),
    path('manage-categories/', views.manage_categories, name='manage_categories'),
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

    path('chat/mark-read/', comm.mark_read, name='mark_read'),

    path('notifications/', comm.notification_list, name='notification_list'),
    path('notifications/unread/', comm.unread_notification_count, name='unread_notification_count'),
    path('notifications/<int:notification_id>/mark-read/', comm.mark_notification_read, name='mark_notification_read'),
    path('notifications/mark-all-read/', comm.mark_all_notifications_read, name='mark_all_notifications_read'),

    path('transfers/', comm.transfer_history, name='transfer_history'),
    path('transfers/create/', comm.create_transfer, name='create_transfer'),
    path('transfers/source-items/', comm.transfer_source_items, name='transfer_source_items'),
    path('transfers/<int:transfer_id>/', comm.transfer_detail, name='transfer_detail'),
    path('transfers/<int:transfer_id>/update-status/', comm.update_transfer_status, name='update_transfer_status'),
    path('whatsapp/', wa.whatsapp_inbox, name='whatsapp_inbox'),

    path('logout/', views.logout_view, name='logout'),
    path('change-password/', views.change_password, name='change_password'),
    path('shop/<slug:shop_slug>/', views.shop_dashboard, name='shop_dashboard'),
]

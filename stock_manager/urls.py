from django.urls import path
from . import views
from . import communication_views as comm
from . import whatsapp_views as wa

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('pos/<slug:shop_slug>/', views.point_of_sale, name='point_of_sale'),
    path('sales-history/', views.sales_history, name='sales_history'),
    path('receipt/<int:receipt_id>/print/', views.print_receipt, name='print_receipt'),
    path('manage/', views.admin_manage, name='admin_manage'),
    path('financial-report/', views.financial_report, name='financial_report'),
    path('settings/', views.settings_view, name='settings'),
    path('download-template/', views.download_template, name='download_template'),
    path('search/', views.search_items, name='search'),
    path('export-csv/', views.export_csv, name='export_csv'),
    path('dashboard-bulk-import/', views.dashboard_bulk_import, name='dashboard_bulk_import'),

    path('heartbeat/', comm.heartbeat, name='heartbeat'),
    path('mark-offline/', comm.mark_offline, name='mark_offline'),
    path('api/online-users/', comm.get_online_users, name='online_users'),
    path('messages/', comm.messages_view, name='messages'),
    path('messages/send/', comm.send_message, name='send_message'),
    path('messages/api/<int:user_id>/', comm.get_messages, name='get_messages'),
    path('messages/unread/', comm.get_unread_count, name='unread_count'),
    path('calls/', comm.calls_view, name='calls'),
    path('calls/initiate/', comm.initiate_call, name='initiate_call'),
    path('calls/<int:call_id>/', comm.call_room, name='call_room'),
    path('calls/<int:call_id>/end/', comm.end_call, name='end_call'),
    path('calls/<int:call_id>/signal/', comm.call_signal, name='call_signal'),
    path('meetings/', comm.meetings_view, name='meetings'),
    path('meetings/create/', comm.create_meeting, name='create_meeting'),
    path('meetings/join/<str:meeting_code>/', comm.join_meeting, name='join_meeting'),
    path('meetings/<str:meeting_code>/', comm.meeting_room, name='meeting_room'),
    path('meetings/<str:meeting_code>/leave/', comm.leave_meeting, name='leave_meeting'),
    path('meetings/<str:meeting_code>/signal/', comm.meeting_signal, name='meeting_signal'),

    path('whatsapp/', wa.whatsapp_inbox, name='whatsapp_inbox'),
    path('whatsapp/webhook/', wa.whatsapp_webhook, name='whatsapp_webhook'),

    path('logout/', views.logout_view, name='logout'),
    path('change-password/', views.change_password, name='change_password'),
    path('<slug:shop_slug>/', views.shop_dashboard, name='shop_dashboard'),
]

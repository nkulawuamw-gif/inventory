from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/calls/$', consumers.CallConsumer.as_asgi()),
    re_path(r'^ws/call-signal/(?P<call_id>\d+)/$', consumers.CallSignalConsumer.as_asgi()),
    re_path(r'^ws/chat/$', consumers.ChatConsumer.as_asgi()),
]

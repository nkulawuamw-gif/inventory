from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r"ws/chat/$", consumers.ChatConsumer.as_asgi()),
    re_path(r"ws/calls/$", consumers.CallConsumer.as_asgi()),
    re_path(r"ws/presence/$", consumers.PresenceConsumer.as_asgi()),
    re_path(r"ws/call-signal/(?P<room_id>\w+)/$", consumers.CallSignalConsumer.as_asgi()),
]

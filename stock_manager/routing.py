from django.urls import re_path
from .consumers import ChatConsumer, CallConsumer, PresenceConsumer

websocket_urlpatterns = [
    re_path(r"ws/chat/$", ChatConsumer.as_asgi()),
    re_path(r"ws/calls/$", CallConsumer.as_asgi()),
    re_path(r"ws/presence/$", PresenceConsumer.as_asgi()),
]

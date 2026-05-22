from django.urls import path
from .consumers import ChatConsumer, CallConsumer, CallSignalConsumer, PresenceConsumer

websocket_urlpatterns = [
    path("ws/chat/", ChatConsumer.as_asgi()),
    path("ws/calls/", CallConsumer.as_asgi()),
    path("ws/presence/", PresenceConsumer.as_asgi()),
    path("ws/call-signal/<int:call_id>/", CallSignalConsumer.as_asgi()),
]

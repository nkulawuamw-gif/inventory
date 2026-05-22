import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import User
from django.utils import timezone
from .models import Message, Call, UserPresence


# ---------------- PRESENCE ----------------
class PresenceConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.user = self.scope["user"]
        if self.user.is_anonymous:
            await self.close()
            return

        self.group_name = "presence"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        await self.set_online(True)
        await self.channel_layer.group_send(self.group_name, {
            "type": "presence_update",
            "user_id": self.user.id,
            "username": self.user.username,
            "display_name": self.user.get_full_name().strip() or self.user.username,
            "is_online": True,
        })

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        await self.set_online(False)
        await self.channel_layer.group_send(self.group_name, {
            "type": "presence_update",
            "user_id": self.user.id,
            "username": self.user.username,
            "display_name": self.user.get_full_name().strip() or self.user.username,
            "is_online": False,
        })

    async def presence_update(self, event):
        await self.send(text_data=json.dumps(event))

    @database_sync_to_async
    def set_online(self, online):
        UserPresence.objects.update_or_create(
            user=self.user,
            defaults={"is_online": online, "last_seen": timezone.now()}
        )


# ---------------- UNREAD BROADCAST HELPER ----------------
def _send_unread(channel_layer, user_id):
    from asgiref.sync import async_to_sync
    count = Message.objects.filter(receiver_id=user_id, is_read=False).count()
    async_to_sync(channel_layer.group_send)(
        f"user_{user_id}",
        {"type": "unread_update", "count": count}
    )


# ---------------- CHAT ----------------
class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.user = self.scope["user"]
        if self.user.is_anonymous:
            await self.close()
            return

        self.group_name = f"user_{self.user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        count = await self.get_unread_count()
        await self.send(text_data=json.dumps({"type": "unread_update", "count": count}))

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        receiver_id = data["receiver_id"]
        message = data["message"]

        msg = await self.create_message(receiver_id, message)

        await self.channel_layer.group_send(
            f"user_{receiver_id}",
            {
                "type": "chat_message",
                "message": message,
                "sender": self.user.username,
                "sender_id": self.user.id,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
            }
        )

        await self.channel_layer.group_send(
            f"user_{receiver_id}",
            {"type": "unread_update", "count": await self.count_unread(receiver_id)}
        )

        await self.channel_layer.group_send(
            f"user_{self.user.id}",
            {"type": "unread_update", "count": await self.count_unread(self.user.id)}
        )

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))

    async def unread_update(self, event):
        await self.send(text_data=json.dumps(event))

    @database_sync_to_async
    def create_message(self, receiver_id, content):
        return Message.objects.create(
            sender=self.user, receiver_id=receiver_id, content=content
        )

    @database_sync_to_async
    def get_unread_count(self):
        return Message.objects.filter(receiver=self.user, is_read=False).count()

    @database_sync_to_async
    def count_unread(self, user_id):
        return Message.objects.filter(receiver_id=user_id, is_read=False).count()


# ---------------- CALLS ----------------
class CallConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.user = self.scope["user"]
        if self.user.is_anonymous:
            await self.close()
            return

        self.group_name = f"user_{self.user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get("action")

        if action == "call_user":
            receiver_id = data["receiver_id"]
            call_type = data["call_type"]

            call = await self.create_call(receiver_id, call_type)

            await self.channel_layer.group_send(
                f"user_{receiver_id}",
                {
                    "type": "incoming_call",
                    "caller": self.user.username,
                    "caller_id": self.user.id,
                    "room_name": call.room_name,
                    "call_type": call_type,
                }
            )

        if action == "accept_call":
            room_name = data["room_name"]
            caller_id = data["caller_id"]
            await self.channel_layer.group_send(
                f"user_{caller_id}",
                {"type": "call_accepted", "room_name": room_name}
            )

    async def incoming_call(self, event):
        await self.send(text_data=json.dumps(event))

    async def call_accepted(self, event):
        await self.send(text_data=json.dumps(event))

    @database_sync_to_async
    def create_call(self, receiver_id, call_type):
        return Call.objects.create(
            caller=self.user,
            receiver_id=receiver_id,
            call_type=call_type,
        )

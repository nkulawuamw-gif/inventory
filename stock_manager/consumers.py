import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from django.db.models import Q

from .models import Message, Profile, Notification, User

logger = logging.getLogger(__name__)


# =========================
# PRESENCE
# =========================

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
            "display_name": self.user.get_full_name() or self.user.username,
            "is_online": True,
        })

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

        await self.set_online(False)

    async def presence_update(self, event):
        await self.send(text_data=json.dumps(event))

    @database_sync_to_async
    def set_online(self, online):
        profile, _ = Profile.objects.get_or_create(user=self.user)
        profile.is_online = online
        profile.last_seen = timezone.now()
        profile.save(update_fields=["is_online", "last_seen"])


# =========================
# NOTIFICATIONS
# =========================

class NotificationConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.user = self.scope["user"]

        if self.user.is_anonymous:
            await self.close()
            return

        self.group_name = f"notifications_{self.user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        count = await self.get_unread_count()
        await self.send(text_data=json.dumps({
            "type": "notification_count",
            "count": count
        }))

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def new_notification(self, event):
        await self.send(text_data=json.dumps({
            "type": "new_notification",
            **event
        }))

    async def notification_count_update(self, event):
        await self.send(text_data=json.dumps(event))

    @database_sync_to_async
    def get_unread_count(self):
        return Notification.objects.filter(
            user=self.user,
            is_read=False
        ).count()


# =========================
# CHAT CONSUMER (FIXED)
# =========================

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

        await self.send(text_data=json.dumps({
            "type": "unread_update",
            "count": count
        }))

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except Exception:
            return

        msg_type = data.get("type")

        if msg_type == "mark_read":
            await self.handle_mark_read(data)
            return

        receiver_id = data.get("receiver_id")
        message_text = data.get("message")

        if not receiver_id or not message_text:
            return

        msg = await self.create_message(receiver_id, message_text)

        payload = {
            "type": "chat_message",
            "message_id": msg.id,
            "message": message_text,
            "sender": self.user.username,
            "sender_id": self.user.id,
            "sender_name": self.user.get_full_name() or self.user.username,
            "timestamp": msg.timestamp.isoformat(),
        }

        await self.channel_layer.group_send(f"user_{receiver_id}", payload)

        await self.create_notification(receiver_id, message_text)

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))

    async def unread_update(self, event):
        await self.send(text_data=json.dumps(event))

    # =========================
    # DB METHODS (SAFE)
    # =========================

    @database_sync_to_async
    def create_message(self, receiver_id, content):
        return Message.objects.create(
            sender=self.user,
            receiver_id=receiver_id,
            content=content
        )

    @database_sync_to_async
    def create_notification(self, receiver_id, message_text):
        try:
            receiver = User.objects.get(id=receiver_id)

            if receiver == self.user:
                return

            Notification.objects.create(
                user=receiver,
                sender=self.user,
                title=f"New message from {self.user.username}",
                message=message_text[:200],
            )

        except Exception as e:
            logger.exception(f"Notification error: {e}")

    @database_sync_to_async
    def get_unread_count(self):
        return Message.objects.filter(
            receiver=self.user,
            is_read=False
        ).count()

    @database_sync_to_async
    def handle_mark_read(self, data):
        sender_id = data.get("sender_id")
        if sender_id:
            Message.objects.filter(
                sender_id=sender_id,
                receiver=self.user,
                is_read=False
            ).update(is_read=True)
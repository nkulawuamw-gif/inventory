import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from django.db.models import Q

from .models import Message, Profile, Notification, User, Conversation

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
# CHAT CONSUMER
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

        if msg_type == "get_conversations":
            await self.send_conversations()
            return

        if msg_type == "typing":
            await self.handle_typing(data)
            return

        if msg_type == "mark_delivered":
            await self.handle_mark_delivered(data)
            return

        # Regular message sending
        receiver_id = data.get("receiver_id")
        message_text = data.get("message")

        if not receiver_id or not message_text:
            return

        receiver_id = int(receiver_id)
        msg = await self.create_message(receiver_id, message_text)

        # Send to receiver
        payload = {
            "type": "chat_message",
            "message_id": msg.id,
            "message": message_text,
            "sender": self.user.username,
            "sender_id": self.user.id,
            "sender_name": self.user.get_full_name() or self.user.username,
            "timestamp": msg.timestamp.isoformat(),
            "status": "sent",
        }

        await self.channel_layer.group_send(f"user_{receiver_id}", payload)

        # Send back to sender with message_id
        await self.send(text_data=json.dumps({
            "type": "self_message",
            "message_id": msg.id,
            "message": message_text,
            "receiver_id": receiver_id,
            "timestamp": msg.timestamp.isoformat(),
            "status": "sent",
        }))

        # Update conversation list for both users
        await self.send_conversations()
        conv_list = await self.get_conversations()
        await self.channel_layer.group_send(
            f"user_{receiver_id}",
            {"type": "conversation_list", "conversations": conv_list}
        )

        # Create notification
        notif_id = await self.create_notification(receiver_id, message_text)
        if notif_id:
            await self.channel_layer.group_send(
                f"notifications_{receiver_id}",
                {
                    "type": "new_notification",
                    "title": f"New message from {self.user.get_full_name() or self.user.username}",
                    "message": message_text[:200],
                    "sender_name": self.user.get_full_name() or self.user.username,
                    "notification_type": "message",
                    "notification_id": notif_id,
                    "link": "/chat/",
                    "transfer_code": None,
                },
            )

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))

    async def conversation_list(self, event):
        await self.send(text_data=json.dumps({
            "type": "conversations",
            "conversations": event["conversations"],
        }))

    async def self_message(self, event):
        await self.send(text_data=json.dumps(event))

    async def unread_update(self, event):
        await self.send(text_data=json.dumps(event))

    async def message_status_update(self, event):
        await self.send(text_data=json.dumps({
            "type": "message_status",
            "message_id": event["message_id"],
            "status": event["status"],
        }))

    async def typing(self, event):
        await self.send(text_data=json.dumps({
            "type": "typing",
            "user_id": event["user_id"],
            "sender_name": event["sender_name"],
            "is_typing": event["is_typing"],
        }))

    # =========================
    # HANDLERS
    # =========================

    async def handle_typing(self, data):
        receiver_id = data.get("receiver_id")
        is_typing = data.get("is_typing", False)
        if not receiver_id:
            return
        await self.channel_layer.group_send(
            f"user_{receiver_id}",
            {
                "type": "typing",
                "user_id": self.user.id,
                "sender_name": self.user.get_full_name() or self.user.username,
                "is_typing": is_typing,
            }
        )

    async def handle_mark_read(self, data):
        sender_id = data.get("sender_id")
        if sender_id:
            updated = await self.mark_messages_read(sender_id)
            if updated:
                # Notify sender that messages were read
                await self.channel_layer.group_send(
                    f"user_{sender_id}",
                    {
                        "type": "message_status_update",
                        "message_id": None,
                        "status": "read",
                    }
                )
                # Update conversation lists
                conv_list = await self.get_conversations()
                await self.channel_layer.group_send(
                    f"user_{sender_id}",
                    {"type": "conversation_list", "conversations": conv_list}
                )
                await self.send_conversations()

    async def handle_mark_delivered(self, data):
        sender_id = data.get("sender_id")
        if sender_id:
            await self.mark_messages_delivered(sender_id)

    async def send_conversations(self):
        conversations = await self.get_conversations()
        await self.send(text_data=json.dumps({
            "type": "conversations",
            "conversations": conversations,
        }))

    # =========================
    # DB METHODS
    # =========================

    @database_sync_to_async
    def create_message(self, receiver_id, content):
        receiver = User.objects.get(id=receiver_id)
        conv = Conversation.objects.filter(
            participants=self.user
        ).filter(
            participants=receiver
        ).first()
        if not conv:
            conv = Conversation.objects.create()
            conv.participants.add(self.user, receiver)

        msg = Message.objects.create(
            conversation=conv,
            sender=self.user,
            receiver=receiver,
            content=content,
            status='sent',
        )
        conv.last_message = content[:200]
        conv.last_message_time = msg.timestamp
        conv.last_message_sender = self.user
        conv.save(update_fields=['last_message', 'last_message_time', 'last_message_sender', 'updated_at'])
        return msg

    @database_sync_to_async
    def create_notification(self, receiver_id, message_text):
        try:
            receiver = User.objects.get(id=receiver_id)
            if receiver == self.user:
                return None
            notif = Notification.objects.create(
                user=receiver,
                sender=self.user,
                title=f"New message from {self.user.get_full_name() or self.user.username}",
                message=message_text[:200],
            )
            return notif.id
        except Exception as e:
            logger.exception(f"Notification error: {e}")
            return None

    @database_sync_to_async
    def get_conversations(self):
        convs = Conversation.objects.filter(
            participants=self.user
        ).prefetch_related('participants').order_by('-updated_at')

        result = []
        for conv in convs:
            other = conv.participants.exclude(id=self.user.id).first()
            if not other:
                continue
            unread = Message.objects.filter(
                conversation=conv,
                receiver=self.user,
                status__in=['sent', 'delivered'],
                is_read=False
            ).count()
            profile = Profile.objects.filter(user=other).first()
            result.append({
                "conversation_id": conv.id,
                "user_id": other.id,
                "username": other.username,
                "full_name": other.get_full_name() or other.username,
                "last_message": conv.last_message,
                "last_message_time": conv.last_message_time.isoformat() if conv.last_message_time else None,
                "last_message_sender": conv.last_message_sender.username if conv.last_message_sender else None,
                "unread_count": unread,
                "is_online": profile.is_online if profile else False,
                "last_seen": profile.last_seen.isoformat() if profile and profile.last_seen else None,
            })
        return result

    @database_sync_to_async
    def get_unread_count(self):
        return Message.objects.filter(
            receiver=self.user,
            is_read=False
        ).count()

    @database_sync_to_async
    def mark_messages_read(self, sender_id):
        qs = Message.objects.filter(
            sender_id=sender_id,
            receiver=self.user,
            is_read=False
        )
        count = qs.count()
        if count:
            qs.update(is_read=True, status='read', read_at=timezone.now())
        return count > 0

    @database_sync_to_async
    def mark_messages_delivered(self, sender_id):
        Message.objects.filter(
            sender_id=sender_id,
            receiver=self.user,
            status='sent'
        ).update(status='delivered')
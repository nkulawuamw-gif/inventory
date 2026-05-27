import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from django.db.models import Q
from django.db import connection
from .models import Message, Profile, Notification

logger = logging.getLogger(__name__)


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
        profile, created = Profile.objects.get_or_create(user=self.user)
        profile.is_online = online
        profile.last_seen = timezone.now()
        profile.save(update_fields=["is_online", "last_seen"])


# ---------------- NOTIFICATION ----------------
class NotificationConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.user = self.scope["user"]
        if self.user.is_anonymous:
            await self.close()
            return

        self.group_name = f"notifications_{self.user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        count = await self.get_unread_notification_count()
        await self.send(text_data=json.dumps({
            "type": "notification_count",
            "count": count
        }))

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type")

        if msg_type == "mark_read":
            await self.handle_mark_read(data)
        elif msg_type == "mark_all_read":
            await self.handle_mark_all_read()

    async def new_notification(self, event):
        await self.send(text_data=json.dumps({
            "type": "new_notification",
            "id": event.get("id"),
            "title": event.get("title"),
            "message": event.get("message"),
            "notification_type": event.get("notification_type"),
            "sender_name": event.get("sender_name", ""),
            "link": event.get("link", ""),
            "transfer_code": event.get("transfer_code", ""),
            "created_at": event.get("created_at"),
        }))
        count = await self.get_unread_notification_count()
        await self.send(text_data=json.dumps({
            "type": "notification_count",
            "count": count
        }))

    async def notification_count_update(self, event):
        await self.send(text_data=json.dumps({
            "type": "notification_count",
            "count": event.get("count", 0)
        }))

    async def handle_mark_read(self, data):
        notification_id = data.get("notification_id")
        if notification_id:
            await self.mark_notification_read(notification_id)
            count = await self.get_unread_notification_count()
            await self.send(text_data=json.dumps({
                "type": "notification_count",
                "count": count
            }))

    async def handle_mark_all_read(self):
        await self.mark_all_notifications_read()
        count = await self.get_unread_notification_count()
        await self.send(text_data=json.dumps({
            "type": "notification_count",
            "count": count
        }))

    @database_sync_to_async
    def get_unread_notification_count(self):
        if 'stock_manager_notification' not in connection.introspection.table_names():
            return 0
        return Notification.objects.filter(user=self.user, is_read=False).count()

    @database_sync_to_async
    def mark_notification_read(self, notification_id):
        if 'stock_manager_notification' not in connection.introspection.table_names():
            return
        Notification.objects.filter(id=notification_id, user=self.user).update(is_read=True)

    @database_sync_to_async
    def mark_all_notifications_read(self):
        if 'stock_manager_notification' not in connection.introspection.table_names():
            return
        Notification.objects.filter(user=self.user, is_read=False).update(is_read=True)


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
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError as e:
            logger.error(f"ChatConsumer JSON decode error: {e}")
            return

        msg_type = data.get("type")

        if msg_type == "mark_read":
            await self.handle_mark_read(data)
            return

        if msg_type == "delivery_ack":
            await self.handle_delivery_ack(data)
            return

        if msg_type == "typing":
            await self.handle_typing(data)
            return

        if msg_type == "get_conversations":
            await self.send_conversations()
            return

        receiver_id = data.get("receiver_id")
        message_text = data.get("message")
        if not receiver_id or not message_text:
            return

        try:
            msg = await self.create_message(receiver_id, message_text)
        except Exception as e:
            logger.exception(f"ChatConsumer: create_message failed: {e}")
            return

        payload = {
            "type": "chat_message",
            "message_id": msg.id,
            "message": message_text,
            "sender": self.user.username,
            "sender_id": self.user.id,
            "sender_name": self.user.get_full_name().strip() or self.user.username,
            "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
        }

        await self.channel_layer.group_send(f"user_{receiver_id}", payload)

        # create notification for receiver
        await self.create_notification(receiver_id, self.user, message_text)

        await self.channel_layer.group_send(
            f"user_{receiver_id}",
            {"type": "unread_update", "count": await self.count_unread(receiver_id)}
        )
        await self.channel_layer.group_send(
            f"user_{self.user.id}",
            {"type": "unread_update", "count": await self.count_unread(self.user.id)}
        )

    async def chat_message(self, event):
        try:
            if event.get("sender_id") != self.user.id:
                await self.channel_layer.group_send(
                    f"user_{event['sender_id']}",
                    {
                        "type": "delivery_ack",
                        "message_id": event["message_id"],
                        "sender_id": self.user.id,
                    }
                )
            await self.send(text_data=json.dumps(event))
        except Exception as e:
            logger.error(f"ChatConsumer: chat_message error: {e}")

    async def delivery_ack(self, event):
        await self.update_message_status(event["message_id"], "delivered")
        await self.send(text_data=json.dumps({
            "type": "message_status",
            "message_id": event["message_id"],
            "status": "delivered",
        }))

    async def read_receipt(self, event):
        await self.send(text_data=json.dumps({
            "type": "message_status",
            "message_ids": event["message_ids"],
            "status": "read",
            "read_by": event["read_by"],
        }))

    async def unread_update(self, event):
        await self.send(text_data=json.dumps(event))

    async def typing_indicator(self, event):
        await self.send(text_data=json.dumps({
            "type": "typing",
            "user_id": event["user_id"],
            "username": event["username"],
            "is_typing": event["is_typing"],
        }))

    async def handle_typing(self, data):
        receiver_id = data.get("receiver_id")
        is_typing = data.get("is_typing", False)
        if not receiver_id:
            return
        await self.channel_layer.group_send(
            f"user_{receiver_id}",
            {
                "type": "typing_indicator",
                "user_id": self.user.id,
                "username": self.user.get_full_name().strip() or self.user.username,
                "is_typing": is_typing,
            }
        )

    async def handle_mark_read(self, data):
        sender_id = data.get("sender_id")
        if not sender_id:
            return
        updated_ids = await self.mark_messages_read(sender_id)
        if updated_ids:
            await self.channel_layer.group_send(
                f"user_{sender_id}",
                {
                    "type": "read_receipt",
                    "message_ids": updated_ids,
                    "read_by": self.user.id,
                }
            )

    async def handle_delivery_ack(self, data):
        message_id = data.get("message_id")
        if message_id:
            await self.update_message_status(message_id, "delivered")
            await self.send(text_data=json.dumps({
                "type": "message_status",
                "message_id": message_id,
                "status": "delivered",
            }))

    async def send_conversations(self):
        conversations = await self.get_conversations()
        await self.send(text_data=json.dumps({
            "type": "conversations",
            "conversations": conversations,
        }))

    @database_sync_to_async
    def get_conversations(self):
        user = self.user
        messages = Message.objects.filter(
            Q(sender=user) | Q(receiver=user)
        ).select_related('sender', 'receiver').order_by('-timestamp')

        seen = set()
        result = []
        for msg in messages:
            other = msg.receiver if msg.sender == user else msg.sender
            if other.id in seen:
                continue
            seen.add(other.id)
            profile = Profile.objects.filter(user=other).first()
            unread = Message.objects.filter(sender=other, receiver=user, is_read=False).count()
            result.append({
                "user_id": other.id,
                "username": other.username,
                "full_name": other.get_full_name().strip() or other.username,
                "is_online": profile.is_online if profile else False,
                "last_seen": profile.last_seen.isoformat() if profile and profile.last_seen else None,
                "last_message": msg.content[:100],
                "last_time": msg.timestamp.isoformat(),
                "unread_count": unread,
            })
        return result

    @database_sync_to_async
    def create_notification(self, receiver_id, sender, message_text):
        try:
            if 'stock_manager_notification' not in connection.introspection.table_names():
                return
            from django.contrib.auth import get_user_model
            User = get_user_model()
            receiver = User.objects.get(id=receiver_id)
            if receiver == sender:
                return
            notif = Notification.objects.create(
                user=receiver,
                sender=sender,
                title=f"New message from {sender.get_full_name().strip() or sender.username}",
                message=message_text[:200],
                type="message",
                link="/chat/",
            )
            channel_layer = self.channel_layer
            import asyncio
            asyncio.ensure_future(
                channel_layer.group_send(
                    f"notifications_{receiver_id}",
                    {
                        "type": "new_notification",
                        "id": notif.id,
                        "title": notif.title,
                        "message": notif.message,
                        "notification_type": notif.type,
                        "sender_name": sender.get_full_name().strip() or sender.username,
                        "link": notif.link,
                        "created_at": notif.created_at.isoformat(),
                    }
                )
            )
        except Exception as e:
            logger.exception(f"create_notification failed: {e}")

    @database_sync_to_async
    def create_message(self, receiver_id, content):
        from django.db import connection, ProgrammingError
        try:
            return Message.objects.create(
                sender=self.user, receiver_id=receiver_id, content=content
            )
        except ProgrammingError as e:
            logger.warning(f"ORM create failed ({e}), falling back to raw SQL")
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO stock_manager_message (sender_id, receiver_id, content, timestamp, is_read) "
                    "VALUES (%s, %s, %s, NOW(), %s) RETURNING id",
                    [self.user.id, receiver_id, content, False]
                )
                msg_id = cursor.fetchone()[0]
            return Message.objects.defer('status').get(id=msg_id)

    @database_sync_to_async
    def update_message_status(self, message_id, status):
        Message.objects.filter(id=message_id).update(
            is_read=True if status == 'read' else False
        )

    @database_sync_to_async
    def mark_messages_read(self, sender_id):
        qs = Message.objects.filter(
            sender_id=sender_id, receiver=self.user, is_read=False
        )
        ids = list(qs.values_list('id', flat=True))
        if ids:
            qs.update(is_read=True)
        return ids

    @database_sync_to_async
    def get_unread_count(self):
        return Message.objects.filter(receiver=self.user, is_read=False).count()

    @database_sync_to_async
    def count_unread(self, user_id):
        return Message.objects.filter(receiver_id=user_id, is_read=False).count()

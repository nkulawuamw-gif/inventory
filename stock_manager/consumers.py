import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from django.db.models import Q
from .models import Message, Profile


# ---------------- PRESENCE ----------------
class PresenceConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        print("WebSocket CONNECTED - PresenceConsumer")
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
        print(f"WebSocket DISCONNECTED - PresenceConsumer code={close_code}")
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
        print("WebSocket CONNECTED - ChatConsumer")
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
        print(f"WebSocket DISCONNECTED - ChatConsumer code={close_code}")
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        print(f"WebSocket RECEIVE - ChatConsumer: {text_data}")
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError as e:
            print(f"ChatConsumer JSON decode error: {e}")
            return

        msg_type = data.get("type")

        if msg_type == "mark_read":
            await self.handle_mark_read(data)
            return

        if msg_type == "delivery_ack":
            await self.handle_delivery_ack(data)
            return

        # default: send message
        receiver_id = data.get("receiver_id")
        message_text = data.get("message")
        if not receiver_id or not message_text:
            print("ChatConsumer: missing receiver_id or message")
            return

        try:
            msg = await self.create_message(receiver_id, message_text)
        except Exception as e:
            print(f"ChatConsumer: create_message failed: {e}")
            import traceback
            traceback.print_exc()
            return

        try:
            # send to receiver's group
            await self.channel_layer.group_send(
                f"user_{receiver_id}",
                {
                    "type": "chat_message",
                    "message_id": msg.id,
                    "message": message_text,
                    "sender": self.user.username,
                    "sender_id": self.user.id,
                    "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                }
            )
        except Exception as e:
            print(f"ChatConsumer: group_send to receiver failed: {e}")

        try:
            await self.channel_layer.group_send(
                f"user_{receiver_id}",
                {"type": "unread_update", "count": await self.count_unread(receiver_id)}
            )
        except Exception as e:
            print(f"ChatConsumer: unread_update to receiver failed: {e}")

        try:
            await self.channel_layer.group_send(
                f"user_{self.user.id}",
                {"type": "unread_update", "count": await self.count_unread(self.user.id)}
            )
        except Exception as e:
            print(f"ChatConsumer: unread_update to sender failed: {e}")

    async def chat_message(self, event):
        try:
            # when receiver gets the message, auto-send delivery_ack back
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
            print(f"ChatConsumer: chat_message error: {e}")

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

    @database_sync_to_async
    def create_message(self, receiver_id, content):
        from django.db import connection, ProgrammingError
        try:
            return Message.objects.create(
                sender=self.user, receiver_id=receiver_id, content=content
            )
        except ProgrammingError as e:
            print(f"ChatConsumer: ORM create failed ({e}), falling back to raw SQL")
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

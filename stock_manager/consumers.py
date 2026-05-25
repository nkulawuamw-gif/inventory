import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from .models import Message, Call, UserPresence


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
        print("WebSocket CONNECTED - CallConsumer")
        self.room_group_name = "calls"
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        print(f"WebSocket DISCONNECTED - CallConsumer code={close_code}")
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        print(f"WebSocket RECEIVE - CallConsumer: {text_data}")
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError as e:
            print(f"CallConsumer JSON decode error: {e}")
            return

        call_type = data.get("type")

        if call_type == "end_call":
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "call_event",
                    "event": "end_call",
                }
            )
        else:
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "call_event",
                    "data": data
                }
            )

    async def call_event(self, event):
        await self.send(text_data=json.dumps(event))


# ---------------- CALL SIGNALING (WebRTC relay) ----------------
class CallSignalConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        print("WebSocket CONNECTED - CallSignalConsumer")
        self.user = self.scope['user']
        if self.user.is_anonymous:
            await self.close()
            return

        self.room_id = self.scope['url_route']['kwargs']['room_id']
        self.call_group_name = f'call_signal_{self.room_id}'

        await self.channel_layer.group_add(self.call_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        print(f"WebSocket DISCONNECTED - CallSignalConsumer code={close_code}")
        if hasattr(self, 'call_group_name'):
            await self.channel_layer.group_discard(self.call_group_name, self.channel_name)
            await self.channel_layer.group_send(
                self.call_group_name,
                {'type': 'peer_left', 'user_id': self.user.id}
            )
            await self.channel_layer.group_send(
                self.call_group_name,
                {'type': 'call_ended', 'user_id': self.user.id}
            )

        await self.end_call_in_db()

    @database_sync_to_async
    def end_call_in_db(self):
        try:
            from .models import Call
            call = Call.objects.filter(room_name=self.room_id, status='ringing').first()
            if not call:
                call = Call.objects.filter(id=self.room_id, status__in=['ringing', 'ongoing']).first()
            if call and call.status in ('ringing', 'ongoing'):
                call.status = 'ended'
                call.save(update_fields=['status'])
        except Exception as e:
            print(f"end_call_in_db error: {e}")

    async def receive(self, text_data):
        print(f"WebSocket RECEIVE - CallSignalConsumer: {text_data}")
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError as e:
            print(f"CallSignalConsumer JSON decode error: {e}")
            return
        msg_type = data.get('type')

        if msg_type == 'join':
            await self.channel_layer.group_send(
                self.call_group_name,
                {
                    'type': 'peer_joined',
                    'user_id': self.user.id,
                    'username': self.user.username,
                }
            )
        elif msg_type == 'signal':
            await self.channel_layer.group_send(
                self.call_group_name,
                {
                    'type': 'relay_signal',
                    'from_user_id': self.user.id,
                    'data': data.get('data'),
                }
            )
        elif msg_type == 'end_call':
            await self.end_call_in_db()
            await self.channel_layer.group_send(
                self.call_group_name,
                {'type': 'call_ended', 'user_id': self.user.id}
            )

    async def peer_joined(self, event):
        if event['user_id'] != self.user.id:
            await self.send(text_data=json.dumps({'type': 'peer_joined'}))

    async def relay_signal(self, event):
        if event['from_user_id'] != self.user.id:
            await self.send(text_data=json.dumps({
                'type': 'signal',
                'data': event['data'],
            }))

    async def peer_left(self, event):
        if event['user_id'] != self.user.id:
            await self.send(text_data=json.dumps({'type': 'peer_left'}))

    async def call_ended(self, event):
        await self.send(text_data=json.dumps({'type': 'call_ended'}))

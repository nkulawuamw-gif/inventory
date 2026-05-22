import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import User
from .models import UserPresence


class CallConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        if self.user.is_anonymous:
            await self.close()
            return

        self.user_group_name = f'user_{self.user.id}'

        await self.channel_layer.group_add(
            self.user_group_name,
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'user_group_name'):
            await self.channel_layer.group_discard(
                self.user_group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        pass

    async def incoming_call(self, event):
        await self.send(text_data=json.dumps({
            'type': 'incoming_call',
            'call_id': event['call_id'],
            'caller': event['caller'],
            'caller_id': event['caller_id'],
            'call_type': event['call_type'],
            'room_name': event.get('room_name', f'call_{event["call_id"]}'),
        }))


class CallSignalConsumer(AsyncWebsocketConsumer):
    """WebRTC signaling for a specific call room — no external dependencies."""

    async def connect(self):
        self.user = self.scope['user']
        if self.user.is_anonymous:
            await self.close()
            return

        self.call_id = self.scope['url_route']['kwargs']['call_id']
        self.call_group_name = f'call_signal_{self.call_id}'

        await self.channel_layer.group_add(
            self.call_group_name, self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'call_group_name'):
            await self.channel_layer.group_discard(
                self.call_group_name, self.channel_name
            )
            await self.channel_layer.group_send(
                self.call_group_name,
                {
                    'type': 'peer_left',
                    'user_id': self.user.id,
                }
            )

    async def receive(self, text_data):
        data = json.loads(text_data)
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

    async def peer_joined(self, event):
        if event['user_id'] != self.user.id:
            await self.send(text_data=json.dumps({
                'type': 'peer_joined',
            }))

    async def relay_signal(self, event):
        if event['from_user_id'] != self.user.id:
            await self.send(text_data=json.dumps({
                'type': 'signal',
                'data': event['data'],
            }))

    async def peer_left(self, event):
        if event['user_id'] != self.user.id:
            await self.send(text_data=json.dumps({
                'type': 'peer_left',
            }))

import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import User
from .models import Message, UserPresence


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
        }))


class ChatConsumer(AsyncWebsocketConsumer):
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

        unread = await self.get_unread_count()
        await self.send(text_data=json.dumps({
            'type': 'unread_update',
            'count': unread,
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'user_group_name'):
            await self.channel_layer.group_discard(
                self.user_group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')

        if action == 'send_message':
            await self.handle_send_message(data)
        elif action == 'typing':
            await self.handle_typing(data)
        elif action == 'mark_read':
            await self.handle_mark_read(data)
        elif action == 'request_unread':
            unread = await self.get_unread_count()
            await self.send(text_data=json.dumps({
                'type': 'unread_update',
                'count': unread,
            }))

    async def handle_send_message(self, data):
        receiver_id = data.get('receiver_id')
        body = data.get('body', '').strip()
        if not receiver_id or not body:
            return

        receiver = await self.get_user(receiver_id)
        if not receiver:
            return

        msg = await self.create_message(receiver, body)
        sender_name = await self.get_display_name(self.user)

        event_payload = {
            'type': 'new_message',
            'id': msg.id,
            'sender': self.user.id,
            'sender_name': sender_name,
            'body': body,
            'created_at': msg.created_at.isoformat(),
            'audio_url': None,
        }

        await self.channel_layer.group_send(
            f'user_{receiver_id}', event_payload
        )

        receiver_unread = await self.get_user_unread_count(receiver)
        await self.channel_layer.group_send(
            f'user_{receiver_id}', {
                'type': 'unread_update',
                'count': receiver_unread,
            }
        )

    async def handle_typing(self, data):
        receiver_id = data.get('receiver_id')
        await self.set_typing_status(receiver_id)

        if receiver_id:
            await self.channel_layer.group_send(
                f'user_{receiver_id}', {
                    'type': 'typing_notification',
                    'user_id': self.user.id,
                    'is_typing': True,
                }
            )

    async def handle_mark_read(self, data):
        other_user_id = data.get('user_id')
        if not other_user_id:
            return

        await self.mark_messages_read(other_user_id)

        await self.channel_layer.group_send(
            f'user_{other_user_id}', {
                'type': 'messages_read',
                'user_id': self.user.id,
            }
        )

        unread = await self.get_unread_count()
        await self.send(text_data=json.dumps({
            'type': 'unread_update',
            'count': unread,
        }))

    async def new_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'new_message',
            'id': event['id'],
            'sender': event['sender'],
            'sender_name': event['sender_name'],
            'body': event['body'],
            'created_at': event['created_at'],
            'audio_url': event.get('audio_url'),
        }))

    async def unread_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'unread_update',
            'count': event['count'],
        }))

    async def typing_notification(self, event):
        await self.send(text_data=json.dumps({
            'type': 'typing',
            'user_id': event['user_id'],
            'is_typing': event['is_typing'],
        }))

    async def messages_read(self, event):
        await self.send(text_data=json.dumps({
            'type': 'messages_read',
            'user_id': event['user_id'],
        }))

    @database_sync_to_async
    def get_user(self, user_id):
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None

    @database_sync_to_async
    def create_message(self, receiver, body):
        return Message.objects.create(
            sender=self.user, receiver=receiver, body=body
        )

    @database_sync_to_async
    def get_unread_count(self):
        return Message.objects.filter(
            receiver=self.user, is_read=False
        ).count()

    @database_sync_to_async
    def get_user_unread_count(self, user):
        return Message.objects.filter(
            receiver=user, is_read=False
        ).count()

    @database_sync_to_async
    def set_typing_status(self, receiver_id):
        presence, _ = UserPresence.objects.get_or_create(user=self.user)
        presence.typing_to_id = receiver_id
        presence.is_online = True
        presence.save(update_fields=['typing_to', 'is_online', 'last_seen'])

    @database_sync_to_async
    def mark_messages_read(self, other_user_id):
        Message.objects.filter(
            sender_id=other_user_id, receiver=self.user, is_read=False
        ).update(is_read=True)

    @database_sync_to_async
    def get_display_name(self, user):
        if user.is_superuser:
            return 'Admin'
        try:
            p = user.user_profile
            if p.assigned_shop:
                return p.assigned_shop.name
        except Exception:
            pass
        return user.get_full_name() or user.username

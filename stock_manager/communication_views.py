import json
import logging
from django.db.models import Q
from django.contrib.auth import get_user_model
User = get_user_model()
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from .models import Message, Profile, Notification

logger = logging.getLogger(__name__)


@csrf_exempt
def heartbeat(request):
    try:
        if not request.user.is_authenticated:
            return JsonResponse({"status": "anon"})

        profile, created = Profile.objects.get_or_create(user=request.user)
        profile.last_seen = timezone.now()
        profile.is_online = True
        profile.save(update_fields=["last_seen", "is_online"])

        return JsonResponse({"status": "ok"})
    except Exception as e:
        logger.exception("heartbeat failed for user %s", request.user)
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def mark_offline(request):
    if request.user.is_authenticated:
        Profile.objects.filter(user=request.user).update(is_online=False)
    return JsonResponse({'status': 'ok'})


def auto_logout(request):
    if request.user.is_authenticated:
        Profile.objects.filter(user=request.user).update(is_online=False)
        from django.contrib.auth import logout as auth_logout
        auth_logout(request)
    return JsonResponse({'status': 'logged_out'})


@login_required
def get_all_users(request):
    users = User.objects.all().values('id', 'username')
    return JsonResponse({'users': list(users)})


@login_required
def get_online_users(request):
    try:
        profiles = Profile.objects.filter(
            is_online=True
        ).select_related("user", "shop")

        data = []
        for p in profiles:
            user = p.user
            display_name = user.get_full_name().strip() or user.username
            data.append({
                "id": user.id,
                "username": user.username,
                "display_name": display_name,
                "last_seen": p.last_seen,
                "shop": p.shop.name if p.shop else "",
                "is_admin": user.is_superuser,
            })

        return JsonResponse({"users": data})
    except Exception as e:
        logger.exception("get_online_users failed")
        return JsonResponse({"error": str(e)}, status=500)






# ---------------- CHAT ----------------

@login_required
def chat_view(request):
    conversations = {}
    messages = Message.objects.filter(
        Q(sender=request.user) | Q(receiver=request.user)
    ).select_related('sender', 'receiver').defer('status').order_by('-timestamp')

    for msg in messages:
        other = msg.receiver if msg.sender == request.user else msg.sender
        if other.id not in conversations:
            conversations[other.id] = {
                'user': other,
                'last_message': msg.content,
                'timestamp': msg.timestamp,
                'unread': 0,
                'last_status': 'sent' if msg.sender == request.user else '',
            }
        if msg.receiver == request.user and not msg.is_read:
            conversations[other.id]['unread'] += 1

    total_unread = sum(c['unread'] for c in conversations.values())

    return render(request, 'stock_manager/chat.html', {
        'conversations': sorted(conversations.values(),
                                key=lambda c: c['timestamp'], reverse=True),
        'total_unread': total_unread,
        'page_title': 'Chat',
    })


@login_required
def unread_count(request):
    try:
        count = Message.objects.filter(receiver=request.user, is_read=False).count()
        return JsonResponse({'count': count})
    except Exception as e:
        logger.exception("unread_count failed for user %s", request.user)
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def mark_read(request):
    if request.method == 'POST':
        sender_id = request.POST.get('sender_id')
        if sender_id:
            Message.objects.filter(
                sender_id=sender_id, receiver=request.user, is_read=False
            ).update(is_read=True)
    return JsonResponse({'status': 'ok'})


@login_required
def get_conversation(request, user_id):
    other = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        message_text = request.POST.get('message', '').strip()
        if message_text:
            msg = Message.objects.create(sender=request.user, receiver=other, content=message_text)
            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f"user_{other.id}",
                    {
                        "type": "chat_message",
                        "message_id": msg.id,
                        "message": message_text,
                        "sender": request.user.username,
                        "sender_id": request.user.id,
                        "sender_name": request.user.get_full_name().strip() or request.user.username,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                )
                async_to_sync(channel_layer.group_send)(
                    f"user_{other.id}",
                    {"type": "unread_update", "count": Message.objects.filter(receiver=other, is_read=False).count()}
                )
                async_to_sync(channel_layer.group_send)(
                    f"user_{request.user.id}",
                    {"type": "unread_update", "count": Message.objects.filter(receiver=request.user, is_read=False).count()}
                )
            except Exception:
                pass
        return JsonResponse({'status': 'ok', 'message_id': msg.id if message_text else None})

    msgs = Message.objects.filter(
        Q(sender=request.user, receiver=other) | Q(sender=other, receiver=request.user)
    ).select_related('sender').defer('status').order_by('timestamp')

    unread = Message.objects.filter(sender=other, receiver=request.user, is_read=False)
    updated_ids = list(unread.values_list('id', flat=True))
    if updated_ids:
        unread.update(is_read=True)
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"user_{other.id}",
                {
                    "type": "read_receipt",
                    "message_ids": updated_ids,
                    "read_by": request.user.id,
                }
            )
        except Exception:
            pass

    return JsonResponse({
        'messages': [{
            'id': m.id,
            'content': m.content,
            'sender': m.sender.username,
            'timestamp': m.timestamp.isoformat(),
            'is_mine': m.sender == request.user,
            'status': 'sent',
        } for m in msgs]
    })


# ---------------- NOTIFICATIONS ----------------

@login_required
def notification_list(request):
    notifications = Notification.objects.filter(user=request.user)[:50]
    unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
    return JsonResponse({
        'notifications': [{
            'id': n.id,
            'title': n.title,
            'message': n.message,
            'type': n.type,
            'is_read': n.is_read,
            'created_at': n.created_at.isoformat(),
            'link': n.link,
            'sender': n.sender.username if n.sender else None,
        } for n in notifications],
        'unread_count': unread_count,
    })


@login_required
def mark_notification_read(request, notification_id):
    if request.method == 'POST':
        Notification.objects.filter(id=notification_id, user=request.user).update(is_read=True)
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'POST required'}, status=405)


@login_required
def mark_all_notifications_read(request):
    if request.method == 'POST':
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'POST required'}, status=405)


@login_required
def unread_notification_count(request):
    count = Notification.objects.filter(user=request.user, is_read=False).count()
    return JsonResponse({'count': count})



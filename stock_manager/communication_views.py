import json
from django.db.models import Q
from django.contrib.auth import get_user_model
User = get_user_model()
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from .models import Message, Profile


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
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def mark_offline(request):
    if request.user.is_authenticated:
        Profile.objects.filter(user=request.user).update(is_online=False)
    return JsonResponse({'status': 'ok'})


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
        return JsonResponse({"error": str(e)}, status=500)






# ---------------- CHAT ----------------

@login_required
def chat_view(request):
    conversations = {}
    messages = Message.objects.filter(
        Q(sender=request.user) | Q(receiver=request.user)
    ).select_related('sender', 'receiver').order_by('-timestamp')

    for msg in messages:
        other = msg.receiver if msg.sender == request.user else msg.sender
        if other.id not in conversations:
            conversations[other.id] = {
                'user': other,
                'last_message': msg.content,
                'timestamp': msg.timestamp,
                'unread': 0,
                'last_status': msg.status if msg.sender == request.user else '',
            }
        if msg.receiver == request.user and not msg.is_read:
            conversations[other.id]['unread'] += 1

    total_unread = sum(c['unread'] for c in conversations.values())

    return render(request, 'stock_manager/chat.html', {
        'conversations': sorted(conversations.values(), key=lambda c: c['timestamp'], reverse=True),
        'total_unread': total_unread,
        'page_title': 'Chat',
    })


@login_required
def unread_count(request):
    count = Message.objects.filter(receiver=request.user, is_read=False).count()
    return JsonResponse({'count': count})


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
    msgs = Message.objects.filter(
        Q(sender=request.user, receiver=other) | Q(sender=other, receiver=request.user)
    ).select_related('sender').order_by('timestamp')

    unread = Message.objects.filter(sender=other, receiver=request.user, status__in=['sent', 'delivered'])
    updated_ids = list(unread.values_list('id', flat=True))
    if updated_ids:
        unread.update(status='read', is_read=True)
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
            'status': m.status,
        } for m in msgs]
    })





import json
import logging
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Q
from django.contrib.auth import get_user_model

from .models import Message, Profile, Notification, Shop

User = get_user_model()
logger = logging.getLogger(__name__)


# =========================
# HEARTBEAT (SAFE + RATE STABLE)
# =========================

@csrf_exempt
def heartbeat(request):
    try:
        if not request.user.is_authenticated:
            return JsonResponse({"status": "anon"})

        profile, _ = Profile.objects.get_or_create(user=request.user)

        profile.is_online = True
        profile.last_seen = timezone.now()
        profile.save(update_fields=["is_online", "last_seen"])

        return JsonResponse({"status": "ok"})

    except Exception as e:
        logger.exception("heartbeat failed")
        return JsonResponse({"error": "heartbeat failed"}, status=500)


# =========================
# OFFLINE HANDLING
# =========================

@csrf_exempt
def mark_offline(request):
    try:
        if request.user.is_authenticated:
            Profile.objects.filter(user=request.user).update(is_online=False)
        return JsonResponse({"status": "ok"})
    except Exception as e:
        logger.exception("mark_offline failed")
        return JsonResponse({"error": str(e)}, status=500)


# =========================
# AUTO LOGOUT
# =========================

def auto_logout(request):
    try:
        if request.user.is_authenticated:
            Profile.objects.filter(user=request.user).update(is_online=False)

            from django.contrib.auth import logout
            logout(request)

        return JsonResponse({"status": "logged_out"})
    except Exception as e:
        logger.exception("auto_logout failed")
        return JsonResponse({"error": str(e)}, status=500)


# =========================
# USERS
# =========================

@login_required
def get_all_users(request):
    try:
        users = User.objects.all().values("id", "username")
        return JsonResponse({"users": list(users)})
    except Exception as e:
        logger.exception("get_all_users failed")
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def get_online_users(request):
    try:
        profiles = Profile.objects.select_related("user", "shop").filter(is_online=True)

        data = []
        for p in profiles:
            if not p.user:
                continue

            data.append({
                "id": p.user.id,
                "username": p.user.username,
                "display_name": p.user.get_full_name() or p.user.username,
                "last_seen": p.last_seen.isoformat() if p.last_seen else None,
                "shop": p.shop.name if p.shop else "",
                "is_admin": p.user.is_superuser,
            })

        return JsonResponse({"users": data})

    except Exception as e:
        logger.exception("get_online_users failed")
        return JsonResponse({"error": str(e)}, status=500)


# =========================
# CHAT VIEW (SAFE + OPTIMIZED)
# =========================

@login_required
def chat_view(request):
    try:
        messages = Message.objects.filter(
            Q(sender=request.user) | Q(receiver=request.user)
        ).select_related("sender", "receiver").order_by("-timestamp")

        conversations = {}

        for msg in messages:
            other = msg.receiver if msg.sender == request.user else msg.sender

            if other.id not in conversations:
                conversations[other.id] = {
                    "user": other,
                    "last_message": msg.content,
                    "timestamp": msg.timestamp,
                    "unread": 0,
                }

            if msg.receiver == request.user and not msg.is_read:
                conversations[other.id]["unread"] += 1

        return render(request, "stock_manager/chat.html", {
            "conversations": sorted(
                conversations.values(),
                key=lambda x: x["timestamp"],
                reverse=True
            ),
            "total_unread": sum(c["unread"] for c in conversations.values()),
            "page_title": "Chat",
        })

    except Exception as e:
        logger.exception("chat_view failed")
        return render(request, "stock_manager/chat.html", {
            "conversations": [],
            "total_unread": 0,
            "error": str(e),
        })


# =========================
# UNREAD COUNT
# =========================

@login_required
def unread_count(request):
    try:
        count = Message.objects.filter(receiver=request.user, is_read=False).count()
        return JsonResponse({"count": count})
    except Exception as e:
        logger.exception("unread_count failed")
        return JsonResponse({"error": str(e)}, status=500)


# =========================
# MARK READ
# =========================

@login_required
def mark_read(request):
    try:
        if request.method == "POST":
            sender_id = request.POST.get("sender_id")

            if sender_id:
                Message.objects.filter(
                    sender_id=sender_id,
                    receiver=request.user,
                    is_read=False
                ).update(is_read=True)

        return JsonResponse({"status": "ok"})

    except Exception as e:
        logger.exception("mark_read failed")
        return JsonResponse({"error": str(e)}, status=500)


# =========================
# NOTIFICATIONS
# =========================

@login_required
def notification_list(request):
    try:
        notifications = Notification.objects.filter(user=request.user)[:50]

        return JsonResponse({
            "notifications": [
                {
                    "id": n.id,
                    "title": n.title,
                    "message": n.message,
                    "is_read": n.is_read,
                    "created_at": n.created_at.isoformat(),
                    "sender": n.sender.username if n.sender else None,
                }
                for n in notifications
            ],
            "unread_count": Notification.objects.filter(
                user=request.user,
                is_read=False
            ).count()
        })

    except Exception as e:
        logger.exception("notification_list failed")
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def unread_notification_count(request):
    try:
        count = Notification.objects.filter(
            user=request.user,
            is_read=False
        ).count()

        return JsonResponse({"count": count})

    except Exception as e:
        logger.exception("unread_notification_count failed")
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@csrf_exempt
def mark_notification_read(request, notification_id):
    try:
        if request.method == "POST":
            Notification.objects.filter(
                id=notification_id,
                user=request.user
            ).update(is_read=True)

            return JsonResponse({"status": "ok"})

        return JsonResponse({"error": "POST required"}, status=405)

    except Exception as e:
        logger.exception("mark_notification_read failed")
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@csrf_exempt
def mark_all_notifications_read(request):
    try:
        if request.method == "POST":
            Notification.objects.filter(
                user=request.user,
                is_read=False
            ).update(is_read=True)

            return JsonResponse({"status": "ok"})

        return JsonResponse({"error": "POST required"}, status=405)

    except Exception as e:
        logger.exception("mark_all_notifications_read failed")
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def transfer_history(request):
    messages.error(request, 'Transfer management is not available.')
    return redirect('dashboard')
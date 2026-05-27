import json
import logging
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Q
from django.db import transaction
from django.contrib.auth import get_user_model

from .models import Message, Profile, Notification, Shop, Transfer, UserProfile, Item

User = get_user_model()
logger = logging.getLogger(__name__)


def _format_transfer_items_summary(items_data):
    """Build a readable summary from transfer items_data JSON."""
    parts = []
    for line in items_data or []:
        name = line.get('name') or line.get('item_name') or 'Item'
        try:
            qty = int(line.get('quantity', 0))
        except (ValueError, TypeError):
            qty = 0
        if qty > 0:
            parts.append(f'{qty}× {name}')
    return ', '.join(parts) if parts else 'No items listed'


def _push_notification_to_user(user_id, payload):
    """Push real-time notification via WebSocket (no-op if channels unavailable)."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if not channel_layer:
            return
        async_to_sync(channel_layer.group_send)(
            f'notifications_{user_id}',
            {'type': 'new_notification', **payload},
        )
    except Exception:
        logger.exception('WebSocket notification push failed for user %s', user_id)


def _notify_shop_users(
    shop,
    acting_user,
    title,
    message,
    *,
    notification_type='transfer',
    link='/transfers/',
    transfer_code=None,
):
    """Create Notification rows and push to connected clients for a shop."""
    sender_name = (
        (acting_user.get_full_name() or acting_user.username)
        if acting_user
        else 'System'
    )
    profiles = UserProfile.objects.filter(
        assigned_shop=shop,
        user__is_active=True,
    ).select_related('user')
    for profile in profiles:
        Notification.objects.create(
            user=profile.user,
            sender=acting_user,
            title=title,
            message=message,
        )
        _push_notification_to_user(profile.user.id, {
            'title': title,
            'message': message,
            'sender_name': sender_name,
            'notification_type': notification_type,
            'link': link,
            'transfer_code': transfer_code,
        })


def _notify_receiver_shop_transfer_sent(transfer, acting_user):
    """Alert receiver-shop users when a transfer is dispatched (status → sent)."""
    summary = _format_transfer_items_summary(transfer.items_data)
    message = (
        f'Transfer {transfer.transfer_code} from {transfer.sender_shop.name} is on the way: '
        f'{summary}. Mark it received when stock arrives.'
    )
    _notify_shop_users(
        transfer.receiver_shop,
        acting_user,
        'Incoming Stock Transfer',
        message,
        transfer_code=transfer.transfer_code,
    )


def _notify_receiver_shop_transfer_received(transfer, acting_user):
    """Confirm receiver-shop users when a transfer is marked received."""
    summary = _format_transfer_items_summary(transfer.items_data)
    message = (
        f'Transfer {transfer.transfer_code} from {transfer.sender_shop.name} was marked received: '
        f'{summary}.'
    )
    _notify_shop_users(
        transfer.receiver_shop,
        acting_user,
        'Stock Transfer Received',
        message,
        transfer_code=transfer.transfer_code,
    )


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
        notifications = Notification.objects.filter(
            user=request.user,
        ).order_by('-created_at')[:50]

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
    status_filter = request.GET.get('status', '')
    qs = Transfer.objects.select_related('sender_shop', 'receiver_shop', 'created_by')
    if status_filter:
        qs = qs.filter(status=status_filter)
    user_shops = []
    if request.user.is_superuser:
        user_shops = list(Shop.objects.all())
    else:
        profile = getattr(request.user, 'user_profile', None)
        if profile and profile.assigned_shop:
            user_shops = [profile.assigned_shop]
    transfers = qs.order_by('-created_at')
    return render(request, 'stock_manager/transfer_history.html', {
        'transfers': transfers,
        'status_filter': status_filter,
        'user_shops': user_shops,
        'page_title': 'Transfer History',
    })


@login_required
def create_transfer(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        sender_id = request.POST.get('sender_shop_id')
        receiver_id = request.POST.get('receiver_shop_id')
        notes = request.POST.get('notes', '')
        items_json = request.POST.get('items', '[]')
        items = json.loads(items_json) if isinstance(items_json, str) else items_json
        if not sender_id or not receiver_id:
            return JsonResponse({'error': 'Sender and receiver shops are required'}, status=400)
        if sender_id == receiver_id:
            return JsonResponse({'error': 'Sender and receiver must be different'}, status=400)
        if not items:
            return JsonResponse({'error': 'At least one item is required'}, status=400)

        # Restrict non-admin users to their assigned shop
        if not request.user.is_superuser:
            profile = getattr(request.user, 'user_profile', None)
            if not profile or not profile.is_admin:
                if not profile or not profile.assigned_shop or str(profile.assigned_shop.id) != sender_id:
                    return JsonResponse({'error': 'You can only transfer from your assigned shop.'}, status=403)

        sender = Shop.objects.get(id=sender_id)
        receiver = Shop.objects.get(id=receiver_id)

        # Validate items against sender stock (server-side, source of truth)
        validated_items = []
        errors = []
        for idx, line in enumerate(items, start=1):
            item_id = line.get('item_id') or line.get('id')
            try:
                qty = int(line.get('quantity', 0))
            except (ValueError, TypeError):
                qty = 0

            if not item_id or qty < 1:
                continue

            try:
                item = Item.objects.get(id=item_id, shop=sender)
            except Item.DoesNotExist:
                errors.append(f'Row {idx}: Item not found in source shop')
                continue

            if qty > item.quantity:
                errors.append(f'Row {idx}: Not enough stock for {item.name}. Available: {item.quantity}, Requested: {qty}')
                continue

            validated_items.append({
                'item_id': item.id,
                'name': item.name,
                'quantity': qty,
                'available_qty': item.quantity,
            })

        if not validated_items:
            return JsonResponse({'error': 'At least one valid item is required'}, status=400)
        if errors:
            return JsonResponse({'error': 'Validation failed', 'errors': errors}, status=400)

        transfer = Transfer.objects.create(
            sender_shop=sender,
            receiver_shop=receiver,
            created_by=request.user,
            notes=notes,
            items_data=validated_items,
        )
        return JsonResponse({
            'status': 'ok',
            'transfer_code': transfer.transfer_code,
            'receiver_shop_name': receiver.name,
            'transfer_id': transfer.id,
        })
    except Shop.DoesNotExist:
        return JsonResponse({'error': 'Shop not found'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def transfer_source_items(request):
    """
    JSON endpoint used by the Transfer modal to list items
    available in a selected source shop.
    """
    try:
        shop_id = request.GET.get('shop_id')
        if not shop_id:
            return JsonResponse({'error': 'shop_id is required'}, status=400)

        # Restrict non-admin users to their assigned shop (same rule as create_transfer)
        if not request.user.is_superuser:
            profile = getattr(request.user, 'user_profile', None)
            if not profile or not profile.is_admin:
                if not profile or not profile.assigned_shop or str(profile.assigned_shop.id) != str(shop_id):
                    return JsonResponse({'error': 'Access denied.'}, status=403)

        shop = Shop.objects.get(id=shop_id)
        items = Item.objects.filter(shop=shop).order_by('name').values('id', 'name', 'quantity')
        return JsonResponse({
            'status': 'ok',
            'shop_id': shop.id,
            'shop_name': shop.name,
            'items': list(items),
        })
    except Shop.DoesNotExist:
        return JsonResponse({'error': 'Shop not found'}, status=404)
    except Exception as e:
        logger.exception("transfer_source_items failed")
        return JsonResponse({'error': 'Failed to load items'}, status=500)


@login_required
def transfer_detail(request, transfer_id):
    try:
        t = Transfer.objects.select_related('sender_shop', 'receiver_shop', 'created_by').get(id=transfer_id)

        # Restrict non-admin users to transfers involving their shop
        if not request.user.is_superuser:
            profile = getattr(request.user, 'user_profile', None)
            if profile and not profile.is_admin and profile.assigned_shop:
                if profile.assigned_shop not in (t.sender_shop, t.receiver_shop):
                    return JsonResponse({'error': 'Access denied.'}, status=403)

        return JsonResponse({
            'id': t.id,
            'transfer_code': t.transfer_code,
            'sender_shop': t.sender_shop.name,
            'receiver_shop': t.receiver_shop.name,
            'status': t.status,
            'created_by': t.created_by.get_full_name() or t.created_by.username,
            'created_at': t.created_at.isoformat(),
            'notes': t.notes,
            'items': t.items_data,
        })
    except Transfer.DoesNotExist:
        return JsonResponse({'error': 'Transfer not found'}, status=404)


@login_required
def update_transfer_status(request, transfer_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        t = Transfer.objects.get(id=transfer_id)

        # Restrict non-admin users to transfers involving their shop
        if not request.user.is_superuser:
            profile = getattr(request.user, 'user_profile', None)
            if profile and not profile.is_admin and profile.assigned_shop:
                if profile.assigned_shop not in (t.sender_shop, t.receiver_shop):
                    return JsonResponse({'error': 'Access denied.'}, status=403)

        new_status = request.POST.get('status')
        if new_status not in dict(Transfer.STATUS_CHOICES):
            return JsonResponse({'error': f'Invalid status: {new_status}'}, status=400)

        old_status = t.status
        with transaction.atomic():
            t.status = new_status
            t.save(update_fields=['status'])
            # Receiver shop is notified when stock is dispatched (sent) and when received.
            if new_status == 'sent' and old_status != 'sent':
                _notify_receiver_shop_transfer_sent(t, request.user)
            elif new_status == 'received' and old_status != 'received':
                _notify_receiver_shop_transfer_received(t, request.user)

        return JsonResponse({'status': 'ok', 'new_status': new_status})
    except Transfer.DoesNotExist:
        return JsonResponse({'error': 'Transfer not found'}, status=404)
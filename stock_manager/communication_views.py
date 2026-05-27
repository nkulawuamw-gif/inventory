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
from .models import Message, Profile, Notification, Transfer, TransferItem, Shop, Item, StockTransaction

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
        try:
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
        except Exception as e:
            logger.exception("get_conversation POST failed for user %s", request.user)
            return JsonResponse({"error": str(e)}, status=500)

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
    try:
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
                'transfer_code': n.transfer.transfer_code if n.transfer else None,
            } for n in notifications],
            'unread_count': unread_count,
        })
    except Exception as e:
        logger.exception("notification_list failed for user %s", request.user)
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def mark_notification_read(request, notification_id):
    try:
        if request.method == 'POST':
            Notification.objects.filter(id=notification_id, user=request.user).update(is_read=True)
            return JsonResponse({'status': 'ok'})
        return JsonResponse({'error': 'POST required'}, status=405)
    except Exception as e:
        logger.exception("mark_notification_read failed for user %s", request.user)
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def mark_all_notifications_read(request):
    try:
        if request.method == 'POST':
            Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
            return JsonResponse({'status': 'ok'})
        return JsonResponse({'error': 'POST required'}, status=405)
    except Exception as e:
        logger.exception("mark_all_notifications_read failed for user %s", request.user)
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def unread_notification_count(request):
    try:
        count = Notification.objects.filter(user=request.user, is_read=False).count()
        return JsonResponse({'count': count})
    except Exception as e:
        logger.exception("unread_notification_count failed for user %s", request.user)
        return JsonResponse({"error": str(e)}, status=500)


# ---------------- TRANSFERS ----------------

@login_required
def transfer_history(request):
    try:
        user = request.user
        profile = Profile.objects.filter(user=user).first()
        is_admin = user.is_superuser or (profile and profile.is_admin)

        transfers = Transfer.objects.select_related(
            'sender_shop', 'receiver_shop', 'created_by'
        ).prefetch_related('items')

        if not is_admin and profile and profile.assigned_shop:
            transfers = transfers.filter(
                Q(sender_shop=profile.assigned_shop) | Q(receiver_shop=profile.assigned_shop)
            )

        status_filter = request.GET.get('status', '')
        if status_filter:
            transfers = transfers.filter(status=status_filter)

        user_shops = []
        if profile and profile.assigned_shop:
            user_shops = [profile.assigned_shop]
        elif is_admin:
            user_shops = list(Shop.objects.all())

        return render(request, 'stock_manager/transfer_history.html', {
            'transfers': transfers,
            'status_filter': status_filter,
            'user_shops': [s.id for s in user_shops],
            'page_title': 'Transfer History',
        })
    except Exception as e:
        logger.exception("transfer_history failed for user %s", request.user)
        return render(request, 'stock_manager/transfer_history.html', {
            'transfers': [],
            'status_filter': '',
            'page_title': 'Transfer History',
            'error': str(e),
            'user_shops': [],
        })


@login_required
def transfer_detail(request, transfer_id):
    try:
        transfer = get_object_or_404(
            Transfer.objects.select_related(
                'sender_shop', 'receiver_shop', 'created_by'
            ).prefetch_related('items'),
            id=transfer_id
        )
        return JsonResponse({
            'id': transfer.id,
            'transfer_code': transfer.transfer_code,
            'sender_shop': transfer.sender_shop.name,
            'receiver_shop': transfer.receiver_shop.name,
            'created_by': transfer.created_by.username,
            'status': transfer.status,
            'notes': transfer.notes,
            'items': [{'item_name': i.item_name, 'quantity': i.quantity} for i in transfer.items.all()],
            'created_at': transfer.created_at.isoformat(),
            'updated_at': transfer.updated_at.isoformat(),
        })
    except Exception as e:
        logger.exception("transfer_detail failed")
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@csrf_exempt
def create_transfer(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        sender_shop_id = request.POST.get('sender_shop_id')
        receiver_shop_id = request.POST.get('receiver_shop_id')
        notes = request.POST.get('notes', '')
        items_json = request.POST.get('items', '[]')
        items_data = json.loads(items_json)

        if not sender_shop_id or not receiver_shop_id or not items_data:
            return JsonResponse({'error': 'Missing required fields'}, status=400)

        from_shop = get_object_or_404(Shop, id=sender_shop_id)
        to_shop = get_object_or_404(Shop, id=receiver_shop_id)

        if from_shop == to_shop:
            return JsonResponse({'error': 'Source and target shops cannot be the same'}, status=400)

        success_items = []
        errors = []
        created_items = []

        for item_data in items_data:
            item_name = item_data.get('name', '').strip()
            try:
                qty = int(item_data.get('quantity', 0))
            except (ValueError, TypeError):
                qty = 0
            if not item_name or qty <= 0:
                errors.append(f"Invalid item: {item_name}")
                continue

            source_items = Item.objects.filter(name__iexact=item_name, shop=from_shop)
            if not source_items.exists():
                errors.append(f"Item '{item_name}' not found in {from_shop.name}")
                continue

            source_item = source_items.first()
            if qty > source_item.quantity:
                errors.append(f"Not enough {item_name}: available {source_item.quantity}, requested {qty}")
                continue

            created_items.append({
                'source_item': source_item,
                'item_name': item_name,
                'quantity': qty,
            })

        if not created_items:
            return JsonResponse({'error': 'No valid items to transfer', 'errors': errors}, status=400)

        # Create transfer as pending first so TransferItems exist before signal fires
        transfer = Transfer.objects.create(
            sender_shop=from_shop,
            receiver_shop=to_shop,
            created_by=request.user,
            status='pending',
            notes=notes,
        )

        for ci in created_items:
            src = ci['source_item']
            qty = ci['quantity']

            TransferItem.objects.create(
                transfer=transfer,
                item_name=ci['item_name'],
                quantity=qty,
            )

            src.quantity -= qty
            src.save()

            target_item, t_created = Item.objects.get_or_create(
                name=src.name,
                shop=to_shop,
                defaults={
                    'quantity': qty,
                    'unit_price': src.unit_price,
                    'category': src.category,
                }
            )
            if not t_created:
                target_item.quantity += qty
                target_item.save()

            StockTransaction.objects.create(
                item=src,
                source_shop=from_shop,
                target_shop=to_shop,
                quantity=qty,
                transaction_type='transfer',
                reason=notes or f"Multi-item transfer {transfer.transfer_code}",
                transfer=transfer,
            )

            success_items.append(ci['item_name'])

        # Now set status to sent — signal fires with TransferItems present
        transfer.status = 'sent'
        transfer.save()

        return JsonResponse({
            'status': 'ok',
            'transfer_code': transfer.transfer_code,
            'transfer_id': transfer.id,
            'success_items': success_items,
            'errors': errors,
            'receiver_shop_name': to_shop.name,
        })
    except Exception as e:
        logger.exception("create_transfer failed for user %s", request.user)
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@csrf_exempt
def update_transfer_status(request, transfer_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        transfer = get_object_or_404(Transfer, id=transfer_id)
        new_status = request.POST.get('status', '')

        valid_transitions = {
            'pending': ['sent', 'rejected'],
            'sent': ['received', 'rejected'],
            'received': [],
            'rejected': [],
        }

        allowed = valid_transitions.get(transfer.status, [])
        if new_status not in allowed:
            return JsonResponse({
                'error': f"Cannot change status from '{transfer.status}' to '{new_status}'. Allowed: {allowed}"
            }, status=400)

        if new_status == 'received':
            transfer.status = 'received'
            transfer.save()

        elif new_status == 'rejected':
            for item in transfer.items.all():
                target_items = Item.objects.filter(
                    name__iexact=item.item_name,
                    shop=transfer.receiver_shop
                )
                for tgt in target_items:
                    tgt.quantity -= item.quantity
                    tgt.save()

                source_items = Item.objects.filter(
                    name__iexact=item.item_name,
                    shop=transfer.sender_shop
                )
                for src in source_items:
                    src.quantity += item.quantity
                    src.save()

            transfer.status = 'rejected'
            transfer.save()

        elif new_status == 'sent':
            for item in transfer.items.all():
                source_items = Item.objects.filter(
                    name__iexact=item.item_name,
                    shop=transfer.sender_shop
                )
                if source_items.exists():
                    src = source_items.first()
                    if src.quantity >= item.quantity:
                        src.quantity -= item.quantity
                        src.save()

                        target_item, created = Item.objects.get_or_create(
                            name=src.name,
                            shop=transfer.receiver_shop,
                            defaults={
                                'quantity': item.quantity,
                                'unit_price': src.unit_price,
                                'category': src.category,
                            }
                        )
                        if not created:
                            target_item.quantity += item.quantity
                            target_item.save()

                        StockTransaction.objects.create(
                            item=src,
                            source_shop=transfer.sender_shop,
                            target_shop=transfer.receiver_shop,
                            quantity=item.quantity,
                            transaction_type='transfer',
                            reason=f"Transfer {transfer.transfer_code} sent",
                            transfer=transfer,
                        )
            transfer.status = 'sent'
            transfer.save()

        return JsonResponse({'status': 'ok', 'new_status': transfer.status})
    except Exception as e:
        logger.exception("update_transfer_status failed")
        return JsonResponse({"error": str(e)}, status=500)


